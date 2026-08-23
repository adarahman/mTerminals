"""
fii_dii_market_bias.py
────────────────────────
Unified FII/DII "market bias" report — combines two genuinely separate
NSE data sources mTerminals already fetches independently:

    1. Cash-market net flow (nse_fii_dii_flow_fetch.py) — daily Rs. Cr,
       FII vs DII buy/sell in the cash segment.
    2. F&O participant-wise Open Interest (fii_dii_sentiment.py) —
       day-over-day positioning in index/stock futures + options, per
       participant (FII/DII/Pro/Retail), already scored by
       fii_dii_sentiment._composite_score().

Why this exists: neither source alone is decision-usable on its own.
Cash flow tells you what institutions actually bought/sold today, but
says nothing about whether they're hedging or leaning short in F&O; F&O
OI tells you positioning, but a single day's swing can be noise, and the
existing dashboard panel (fiidii-report.js) shows both side by side with
no combined read — so a person watching the modal has to do the
synthesis themselves.

This module does that synthesis, weighted toward multi-day trend rather
than a single session, and is deliberately explicit about when it
*doesn't* have enough data to be confident. A report that always sounds
confident is worse than useless for decision-making — it's actively
misleading — so low data / conflicting signals show up as caveats and a
depressed confidence score, not as a clean bullish/bearish call.

Public API
──────────
    get_market_bias_report(dt=None, cash_lookback_days=20) -> dict
        dt: session date/datetime (defaults to now). Returns:
            {
                "asOf":              ISO date string,
                "overallScore":      -100..100, signed composite,
                "overallLabel":      "Bullish" / "Bearish" / "Neutral" / "Mixed",
                "overallConfidence": 0..100 — see _combine() docstring
                                      for exactly what discounts this,
                "cash": {...},       # see _cash_bias_block()
                "fo":   {...},       # see _fo_bias_block()
                "agreement": bool | None,  # cash vs F&O direction agree?
                                            # None if either leg is unscored
                                            # or one/both sides are flat
                "narrative": [str, ...],   # ordered, human-readable reasoning
                "caveats":   [str, ...],   # data-quality / confidence flags
            }

Neither `_cash_bias_block` nor `_fo_bias_block` re-derives anything —
they read `get_flow_series()` and `get_report_for_trading_day()` as-is
and combine already-computed numbers. If those two source functions ever
change shape, this module needs no changes to its own math, only to the
two block-builders that read them.
"""

from __future__ import annotations

import statistics
from datetime import datetime

from analytics.nse_fii_dii_flow_fetch import get_flow_series
from analytics.fii_dii_sentiment import get_report_for_trading_day

# ── Overall-score → label thresholds ────────────────────────────────────
# Separate constants from fii_dii_sentiment.BULLISH_SCORE_THRESHOLD /
# NEUTRAL_SCORE_THRESHOLD on purpose — a blended market-wide call and a
# single-participant OI read don't need to share sensitivity, and tuning
# one shouldn't silently retune the other.
BIAS_BULLISH_THRESHOLD = 20.0
BIAS_NEUTRAL_THRESHOLD = 8.0

# Cash-flow trend outweighs F&O positioning by default: actual
# institutional cash buying/selling is the more direct read on "who's
# putting money to work", while F&O positioning is frequently hedging or
# tactical rather than a pure directional bet.
_CASH_WEIGHT = 0.55
_FO_WEIGHT = 0.45

# Within the F&O leg, FII is weighted heaviest — their index-future book
# most directly reflects institutional index-level bias. Pro desks run
# active but often vol-harvesting/writing books rather than pure
# direction, so they count for less here even though fii_dii_sentiment.py
# tracks them as a genuinely separate signal in their own right. Retail
# is deliberately excluded from the blend entirely — it's carried as a
# contrarian-read caveat instead (see fii_dii_sentiment.py's own
# reasoning on this).
_FO_PARTICIPANT_WEIGHTS = {"fii": 0.55, "dii": 0.30, "pro": 0.15}

# Below this many days of cash-flow history, rolling stats/streaks are
# still "warming up" — flagged as a caveat and discounted in confidence,
# rather than silently presented with the same confidence a full window
# would earn.
MIN_CASH_DAYS_FOR_CONFIDENCE = 10


def _cash_bias_block(cash_lookback_days: int) -> dict:
    """Trend read off cash-market net flow: latest day, rolling averages,
    a same-direction streak, and a -100..100 trend score.

    The trend score is deliberately NOT just "is the latest day
    positive" — a single ₹3,624 Cr day means little on its own. It's a
    recency-weighted average of the combined (FII+DII) daily net over the
    lookback window (linearly decaying weights, latest day heaviest),
    then normalized by that window's own volatility (population stdev),
    so a big day in a generally choppy market doesn't score as strongly
    as the same-size move in a market that's been quietly trending one
    direction."""
    series = get_flow_series(cash_lookback_days)
    dates, fii, dii = series["dates"], series["fii"], series["dii"]
    n = len(dates)

    if n == 0:
        return {
            "available": False, "days": 0, "latestDate": None,
            "fiiLatest": None, "diiLatest": None, "netLatest": None,
            "avg5d": None, "avg10d": None, "avg20d": None,
            "streakDays": 0, "streakDirection": "flat", "score": 0.0,
        }

    combined = [f + d for f, d in zip(fii, dii)]

    def _avg(vals, k):
        window = vals[-k:]
        return round(sum(window) / len(window), 1) if window else None

    avg5d, avg10d, avg20d = _avg(combined, 5), _avg(combined, 10), _avg(combined, 20)

    # Same-direction streak on combined net flow, walking back from the
    # latest session until the sign changes (or a flat/zero day breaks it).
    streak = 0
    direction = 0
    for v in reversed(combined):
        sign = 1 if v > 0 else (-1 if v < 0 else 0)
        if streak == 0:
            direction = sign
            if sign == 0:
                break
            streak = 1
        elif sign == direction:
            streak += 1
        else:
            break

    # Recency-weighted trend score: oldest day in the window weight=1,
    # latest day weight=n, so a same-size move counts more the more
    # recently it happened.
    weights = list(range(1, n + 1))
    weight_total = sum(weights)
    weighted_avg = sum(w * v for w, v in zip(weights, combined)) / weight_total if weight_total else 0.0

    # pstdev needs n > 1; a single-day window falls back to that day's own
    # magnitude (or 1.0 to avoid a divide-by-zero on a literal ₹0 Cr day).
    spread = statistics.pstdev(combined) if n > 1 else (abs(combined[0]) or 1.0)
    spread = spread or 1.0
    score = round(max(-100.0, min(100.0, 100 * weighted_avg / (2 * spread))), 1)

    return {
        "available": True,
        "days": n,
        "latestDate": dates[-1],
        "fiiLatest": round(fii[-1], 1),
        "diiLatest": round(dii[-1], 1),
        "netLatest": round(combined[-1], 1),
        "avg5d": avg5d, "avg10d": avg10d, "avg20d": avg20d,
        "streakDays": streak,
        "streakDirection": "buying" if direction > 0 else ("selling" if direction < 0 else "flat"),
        "score": score,
    }


def _fo_bias_block(dt) -> dict:
    """Positioning read off F&O participant OI — a pure combiner over
    fii_dii_sentiment.get_report_for_trading_day()'s already-computed
    per-participant score/confidence/sentiment. Does not re-derive any of
    that scoring logic; if the weighting or thresholds there change, this
    picks it up automatically."""
    report = get_report_for_trading_day(dt)
    if not report.get("available"):
        return {"available": False, "sourceDate": None, "score": 0.0, "participants": {}}

    participants = report["participants"]
    weighted_sum = 0.0
    weight_total = 0.0
    for key, weight in _FO_PARTICIPANT_WEIGHTS.items():
        p = participants.get(key)
        if not p:
            continue
        weighted_sum += p["score"] * weight
        weight_total += weight
    fo_score = round(weighted_sum / weight_total, 1) if weight_total else 0.0

    return {
        "available": True,
        "sourceDate": report.get("sourceDate"),
        "compareDate": report.get("compareDate"),
        "score": fo_score,
        "participants": {
            k: {"sentiment": v["sentiment"], "score": v["score"], "confidence": v["confidence"]}
            for k, v in participants.items()
        },
        "fiiDiiDivergence": report.get("divergence", False),
        "proVsFiiDiiDivergence": report.get("proDivergence", False),
        "retailContrarianRead": participants.get("retail", {}).get("contrarianRead"),
    }


def _label_from_score(score: float) -> str:
    if score >= BIAS_BULLISH_THRESHOLD:
        return "Bullish"
    if score <= -BIAS_BULLISH_THRESHOLD:
        return "Bearish"
    if abs(score) <= BIAS_NEUTRAL_THRESHOLD:
        return "Neutral"
    return "Mixed"


def _cash_narrative(cash: dict) -> tuple[list[str], list[str]]:
    narrative, caveats = [], []
    if not cash["available"]:
        caveats.append("No cash-market flow history available yet — cash leg of the bias is unscored.")
        return narrative, caveats

    narrative.append(
        f"FII+DII combined net cash flow was {'+' if cash['netLatest'] >= 0 else ''}"
        f"₹{cash['netLatest']:,.0f} Cr on {cash['latestDate']} "
        f"(FII {'+' if cash['fiiLatest'] >= 0 else ''}₹{cash['fiiLatest']:,.0f} Cr, "
        f"DII {'+' if cash['diiLatest'] >= 0 else ''}₹{cash['diiLatest']:,.0f} Cr)."
    )
    if cash["streakDays"] >= 2 and cash["streakDirection"] != "flat":
        narrative.append(f"That extends a {cash['streakDays']}-day combined {cash['streakDirection']} streak.")
    if cash["days"] < MIN_CASH_DAYS_FOR_CONFIDENCE:
        caveats.append(
            f"Only {cash['days']} day(s) of cash-flow history on file — rolling "
            f"averages/trend score are still warming up and will sharpen as more "
            f"sessions accumulate."
        )
    return narrative, caveats


def _fo_narrative(fo: dict) -> tuple[list[str], list[str]]:
    narrative, caveats = [], []
    if not fo["available"]:
        caveats.append("No F&O participant-OI data available for the latest session — bias is cash-flow-only.")
        return narrative, caveats

    fii_p = fo["participants"].get("fii", {})
    narrative.append(
        f"F&O positioning: FII index-future book reads "
        f"'{fii_p.get('sentiment', 'Neutral')}' (score {fii_p.get('score', 0)}, "
        f"confidence {fii_p.get('confidence', 0)}%), as of {fo['sourceDate']}."
    )
    if fo["fiiDiiDivergence"]:
        narrative.append("FII and DII index-future positioning are moving in opposite directions.")
    if fo["proVsFiiDiiDivergence"]:
        narrative.append(
            "Pro desks are positioned opposite to combined FII+DII flow — "
            "worth watching for gamma-driven moves."
        )
    retail_read = fo.get("retailContrarianRead")
    if retail_read and retail_read != "No strong contrarian read":
        narrative.append(f"Contrarian angle: {retail_read}.")
    return narrative, caveats


def _combine(cash: dict, fo: dict) -> tuple[float, "bool | None", float]:
    """Overall score/agreement/confidence from the two legs.

    Confidence starts from |score| (a bigger blended move earns more
    confidence on its face), then gets discounted — never boosted — for:
      * cash vs F&O disagreeing on direction (halved)
      * thin cash-flow history, < MIN_CASH_DAYS_FOR_CONFIDENCE (×0.7)
      * only one of the two legs being available at all (×0.75)
    These stack multiplicatively, so a disagreeing, thin-history,
    single-leg read can end up with quite low confidence even if the raw
    score looks large — which is the intended behavior: a big number from
    weak evidence should not read as a strong call.
    """
    if cash["available"] and fo["available"]:
        overall_score = round(_CASH_WEIGHT * cash["score"] + _FO_WEIGHT * fo["score"], 1)
        cash_sign = 1 if cash["score"] > 0 else (-1 if cash["score"] < 0 else 0)
        fo_sign = 1 if fo["score"] > 0 else (-1 if fo["score"] < 0 else 0)
        agreement = (cash_sign == fo_sign) if (cash_sign != 0 and fo_sign != 0) else None
    elif cash["available"]:
        overall_score, agreement = cash["score"], None
    elif fo["available"]:
        overall_score, agreement = fo["score"], None
    else:
        overall_score, agreement = 0.0, None

    confidence = min(100.0, abs(overall_score) * 1.5)
    if agreement is False:
        confidence *= 0.5
    if cash["available"] and cash["days"] < MIN_CASH_DAYS_FOR_CONFIDENCE:
        confidence *= 0.7
    if not (cash["available"] and fo["available"]):
        confidence *= 0.75
    confidence = round(max(0.0, min(100.0, confidence)), 1)

    return overall_score, agreement, confidence


def get_market_bias_report(dt=None, cash_lookback_days: int = 20) -> dict:
    """See module docstring for the full return-shape contract."""
    dt = dt or datetime.now()

    cash = _cash_bias_block(cash_lookback_days)
    fo = _fo_bias_block(dt)

    cash_narrative, cash_caveats = _cash_narrative(cash)
    fo_narrative, fo_caveats = _fo_narrative(fo)
    narrative = cash_narrative + fo_narrative
    caveats = cash_caveats + fo_caveats

    overall_score, agreement, overall_confidence = _combine(cash, fo)
    if agreement is False:
        caveats.append(
            "Cash flow and F&O positioning disagree on direction this session — "
            "treat the overall bias as low-conviction until they align."
        )
    if not cash["available"] and not fo["available"]:
        caveats.append("Neither cash-flow nor F&O data is available — no bias can be scored yet.")

    return {
        "asOf": dt.date().isoformat() if hasattr(dt, "date") else str(dt),
        "overallScore": overall_score,
        "overallLabel": _label_from_score(overall_score),
        "overallConfidence": overall_confidence,
        "cash": cash,
        "fo": fo,
        "agreement": agreement,
        "narrative": narrative,
        "caveats": caveats,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_market_bias_report(), indent=2, default=str))
