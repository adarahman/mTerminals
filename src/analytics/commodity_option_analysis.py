"""Transparent, rule-based context for the visible MCX option-chain window."""

from __future__ import annotations

import math

from decision.confidence import compute_confidence, derive_bias
from decision.evidence import (
    DECISION_EVIDENCE_LABELS,
    DECISION_EVIDENCE_WEIGHTS,
    aggregate_evidence,
)
from decision.basic_scores import score_engine_bias, score_pcr


_COMMODITY_REQUIRED_EVIDENCE = {"pcr", "futures"}


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

    pcr_score = score_pcr(raw_pcr) if raw_pcr is not None else 0.0
    futures_score = score_engine_bias(
        "Bullish" if futures_change is not None and futures_change > 0
        else "Bearish" if futures_change is not None and futures_change < 0
        else "Neutral"
    )
    scores = {
        "pcr": pcr_score,
        "engine_bias": 0.0,
        "futures": futures_score,
        "max_pain": 0.0,
        "oi_velocity": 0.0,
        "capital_flow": 0.0,
    }
    availability = {
        "pcr": raw_pcr is not None,
        "engine_bias": False,
        "futures": futures_change is not None,
        "max_pain": False,
        "oi_velocity": False,
        "capital_flow": False,
    }
    evidence = aggregate_evidence(
        weights=DECISION_EVIDENCE_WEIGHTS,
        scores=scores,
        availability=availability,
        labels={
            **DECISION_EVIDENCE_LABELS,
            "futures": "Futures day direction",
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
        3,
        pcr_score,
        0.0,
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

    verdict = "WAIT" if required_missing else "NEUTRAL" if conflict else bias
    if required_missing:
        reason = "Insufficient option OI or futures-change data to form a directional view."
    elif conflict:
        reason = "Put/call positioning and futures direction conflict; wait for confirmation."
    elif verdict == "BULLISH":
        reason = "Put OI is higher than call OI and the underlying futures are up on the day."
    elif verdict == "BEARISH":
        reason = "Call OI is higher than put OI and the underlying futures are down on the day."
    else:
        reason = "Option positioning and the underlying futures move do not confirm the same direction."

    return {
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
        "scope": "Visible ATM ±10 strikes; OI is current positioning, not OI change.",
        "method": (
            "Uses the shared equity Decision Engine scoring and evidence weights. "
            "Unavailable signals reduce evidence coverage; confidence is not a "
            "probability. Commodity orders are not recommended by this view."
        ),
    }
