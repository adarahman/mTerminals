"""Transparent, rule-based context for the visible MCX option-chain window."""

from __future__ import annotations

import math
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from decision.confidence import compute_confidence, derive_bias
from decision.evidence import (
    DECISION_EVIDENCE_LABELS,
    DECISION_EVIDENCE_WEIGHTS,
    aggregate_evidence,
)
from decision.basic_scores import score_engine_bias, score_pcr
from decision.signal_builder import score_capital_flow, score_max_pain, score_oi_velocity
from decision.short_horizon import detect_short_horizon


_COMMODITY_REQUIRED_EVIDENCE = {"pcr", "futures"}
_OI_HISTORY_WINDOW_SECONDS = 300


def _finite_number(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _visible_window_max_pain(rows):
    strikes = sorted({
        strike for row in rows
        if (strike := _finite_number(row.get("strike"))) is not None
    })
    if len(strikes) < 2:
        return None

    call_oi = {}
    put_oi = {}
    for row in rows:
        strike = _finite_number(row.get("strike"))
        oi = _finite_number(row.get("oi"))
        if strike is None or oi is None:
            continue
        if row.get("type") not in {"CE", "PE"}:
            continue
        target = call_oi if row.get("type") == "CE" else put_oi
        target[strike] = target.get(strike, 0.0) + oi

    if not any(call_oi.values()) or not any(put_oi.values()):
        return None

    # Same intrinsic-payout calculation as the equity engine, restricted to
    # the ATM ±10 contracts actually supplied by the commodity endpoint.
    return min(
        strikes,
        key=lambda settlement: sum(
            call_oi.get(strike, 0.0) * max(settlement - strike, 0.0)
            + put_oi.get(strike, 0.0) * max(strike - settlement, 0.0)
            for strike in strikes
        ),
    )


def _expiry_dte(expiry):
    raw = str(expiry or "").strip().upper()
    for pattern in ("%d%b%Y", "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            expiry_date = datetime.strptime(raw, pattern).date()
            today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
            return max(0, (expiry_date - today).days)
        except ValueError:
            continue
    return 3


def _history_scores(chain, previous_rows, elapsed_seconds):
    """Score five-minute OI velocity and premium flow when history exists."""
    if (
        not previous_rows
        or elapsed_seconds is None
        or elapsed_seconds < _OI_HISTORY_WINDOW_SECONDS
    ):
        return 0.0, False, 0.0, False

    previous = {}
    for row in previous_rows:
        strike = _finite_number(row.get("strike"))
        if strike is not None and row.get("type") in {"CE", "PE"}:
            previous[(row["type"], strike)] = row

    strike_metrics = {}
    capital_flow = 0.0
    premium_locked = 0.0
    matched = 0
    current_contracts = 0
    for row in chain.get("rows") or []:
        otype = row.get("type")
        strike = _finite_number(row.get("strike"))
        oi = _finite_number(row.get("oi"))
        ltp = _finite_number(row.get("ltp"))
        old = previous.get((otype, strike))
        old_oi = _finite_number(old.get("oi")) if old else None
        if strike is None or oi is None or ltp is None:
            continue
        current_contracts += 1
        if old_oi is None:
            continue
        matched += 1
        premium_locked += oi * ltp
        doi = oi - old_oi
        metrics = strike_metrics.setdefault(
            strike,
            {
                "window": 5,
                "strike": strike,
                "ceNow": 0.0,
                "ceDOI": 0.0,
                "peNow": 0.0,
                "peDOI": 0.0,
            },
        )
        side = "ce" if otype == "CE" else "pe"
        metrics[f"{side}Now"] = oi
        metrics[f"{side}DOI"] = doi
        # Follow the equity engine convention: PE writing adds support and
        # CE writing adds resistance, both marked using the current option LTP.
        capital_flow += doi * ltp if otype == "PE" else -doi * ltp

    if matched == 0 or matched / max(current_contracts, 1) < 0.5:
        return 0.0, False, 0.0, False

    spot = _finite_number(chain.get("spot")) or _finite_number(chain.get("atm_strike")) or 0.0
    strikes = sorted(strike_metrics)
    step = min(
        (right - left for left, right in zip(strikes, strikes[1:]) if right > left),
        default=0,
    )
    velocity_context = SimpleNamespace(
        verdicts={}, active_signals=[], oi_annotations={}
    )
    velocity_score = score_oi_velocity(
        pd.DataFrame(strike_metrics.values()),
        spot,
        step,
        velocity_context,
        window=5,
    )
    velocity_available = bool(strike_metrics)
    capital_available = premium_locked > 0
    capital_score = score_capital_flow(capital_flow, premium_locked) if capital_available else 0.0
    return velocity_score, velocity_available, capital_score, capital_available


def analyze_commodity_option_chain(
    chain: dict, *, previous_rows=None, elapsed_seconds=None, price_closes=None
) -> dict:
    """Apply shared decision evidence scoring to visible MCX chain data."""
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

    pcr_score = score_pcr(raw_pcr) if raw_pcr is not None else 0.0
    futures_score = score_engine_bias(
        "Bullish" if futures_change is not None and futures_change > 0
        else "Bearish" if futures_change is not None and futures_change < 0
        else "Neutral"
    )
    max_pain = _visible_window_max_pain(rows)
    # ATM is not a substitute for the futures price when scoring the
    # max-pain distance; keep the visible estimate but omit its evidence
    # score if the provider did not supply an underlying price.
    spot = _finite_number(chain.get("spot"))
    dte = _expiry_dte(chain.get("expiry"))
    max_pain_score = 0.0
    if max_pain is not None and spot is not None and spot > 0:
        max_pain_context = SimpleNamespace(verdicts={}, active_signals=[])
        max_pain_score = score_max_pain(
            spot,
            max_pain,
            abs(spot - max_pain),
            dte,
            0.0,
            max_pain_context,
        )
    (
        oi_velocity_score,
        oi_velocity_available,
        capital_flow_score,
        capital_flow_available,
    ) = _history_scores(chain, previous_rows, elapsed_seconds)
    scores = {
        "pcr": pcr_score,
        "engine_bias": 0.0,
        "futures": futures_score,
        "max_pain": max_pain_score,
        "oi_velocity": oi_velocity_score,
        "capital_flow": capital_flow_score,
    }
    availability = {
        "pcr": raw_pcr is not None,
        "engine_bias": False,
        "futures": futures_change is not None,
        "max_pain": max_pain is not None and spot is not None and spot > 0,
        "oi_velocity": oi_velocity_available,
        "capital_flow": capital_flow_available,
    }
    evidence = aggregate_evidence(
        weights=DECISION_EVIDENCE_WEIGHTS,
        scores=scores,
        availability=availability,
        labels={
            **DECISION_EVIDENCE_LABELS,
            "futures": "Futures day direction",
            "max_pain": "Visible-window max pain",
            "oi_velocity": "Five-minute OI velocity",
            "capital_flow": "Five-minute premium flow",
        },
    )
    positive = evidence["positiveContributors"]
    negative = evidence["negativeContributors"]
    conflicting_directions = positive > 0 and negative > 0
    conflict = evidence["conflict"] or conflicting_directions
    bias, strength = derive_bias(evidence["composite"], conflict)
    required_missing = any(
        key in _COMMODITY_REQUIRED_EVIDENCE for key in evidence["missingInputs"]
    )
    confidence = compute_confidence(
        evidence["composite"],
        conflict,
        "",
        positive,
        negative,
        dte,
        pcr_score,
        oi_velocity_score,
        evidence_coverage=evidence["evidenceCoverage"] / 100.0,
        critical_inputs_missing=required_missing,
    )

    evidence_weights = [
        {
            **contributor,
            "direction": (
                "UNAVAILABLE"
                if not contributor["available"]
                else "BULLISH"
                if contributor["score"] > 0.15
                else "BEARISH"
                if contributor["score"] < -0.15
                else "NEUTRAL"
            ),
        }
        for contributor in evidence["contributors"]
    ]

    short_horizon = None
    if price_closes is not None:
        short_horizon = detect_short_horizon(
            str(chain.get("symbol") or "MCX"),
            oi_score=oi_velocity_score if oi_velocity_available else 0.0,
            fut_score=futures_score if futures_change is not None else 0.0,
            fut_ltp=spot or 0.0,
            price_closes=price_closes,
        )

    verdict = "WAIT" if required_missing else "NEUTRAL" if conflict else bias
    if required_missing:
        reason = "Insufficient option OI or futures-change data to form a directional view."
    elif conflict:
        reason = (
            "Available commodity evidence points in different directions; "
            "wait for confirmation."
        )
    elif verdict in {"BULLISH", "BEARISH"}:
        direction = 1 if verdict == "BULLISH" else -1
        sources = [
            item["label"]
            for item in evidence["contributors"]
            if item["available"]
            and item["score"] is not None
            and item["score"] * direction > 0.15
        ]
        reason = (
            f"Shared evidence leans {verdict.lower()}"
            + (f" on {', '.join(sources)}." if sources else ".")
        )
    else:
        reason = "Available commodity evidence is neutral in the visible strike window."

    result = {
        "verdict": verdict,
        "reason": reason,
        "bias_strength": strength,
        "confidence": confidence,
        "evidence_coverage": evidence["evidenceCoverage"],
        "missing_inputs": evidence["missingInputs"],
        "contributors": evidence["contributors"],
        "evidence_weights": evidence_weights,
        "put_call_oi_ratio": pcr,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_oi_wall": wall(calls),
        "put_oi_wall": wall(puts),
        "futures_change_pct": futures_change,
        "visible_window_max_pain": max_pain,
        "oi_velocity_window_seconds": (
            _OI_HISTORY_WINDOW_SECONDS if oi_velocity_available else None
        ),
        "capital_flow_window_seconds": (
            _OI_HISTORY_WINDOW_SECONDS if capital_flow_available else None
        ),
        "scope": (
            "Visible ATM ±10 strikes; max pain is a visible-window estimate; "
            "OI is current positioning; velocity and capital flow compare "
            "five-minute snapshots."
        ),
        "method": (
            "Uses shared equity evidence weights and score functions. Combined "
            "market bias is omitted to avoid double-counting these signals. "
            "History-based evidence appears after five minutes of observations "
            "with at least half the visible contracts matched; confidence is "
            "not a probability. "
            "Commodity orders are not recommended by this view."
        ),
    }
    if short_horizon is not None:
        result["short_horizon"] = short_horizon
    return result
