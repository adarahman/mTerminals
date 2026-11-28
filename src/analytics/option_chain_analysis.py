"""Shared evidence analysis for normalized equity and commodity chains."""

from __future__ import annotations

import math
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from decision.basic_scores import score_engine_bias, score_pcr
from decision.confidence import compute_confidence, derive_bias
from decision.evidence import (
    DECISION_EVIDENCE_LABELS,
    DECISION_EVIDENCE_WEIGHTS,
    aggregate_evidence,
)
from decision.short_horizon import detect_short_horizon
from decision.signal_builder import score_capital_flow, score_max_pain, score_oi_velocity

_HISTORY_WINDOW_SECONDS = 300


def _number(value, *, allow_negative=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (not allow_negative and number < 0):
        return None
    return number


def _expiry_dte(expiry):
    for pattern in ("%d%b%Y", "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            date = datetime.strptime(str(expiry or "").strip().upper(), pattern).date()
            return max(0, (date - datetime.now(ZoneInfo("Asia/Kolkata")).date()).days)
        except ValueError:
            pass
    return 3


def _max_pain(rows):
    strikes = sorted({_number(row.get("strike")) for row in rows} - {None})
    if len(strikes) < 2:
        return None
    calls, puts = {}, {}
    for row in rows:
        strike, oi = _number(row.get("strike")), _number(row.get("oi"))
        if strike is None or oi is None:
            continue
        target = calls if row.get("type") == "CE" else puts if row.get("type") == "PE" else None
        if target is not None:
            target[strike] = target.get(strike, 0) + oi
    if not any(calls.values()) or not any(puts.values()):
        return None
    return min(strikes, key=lambda settle: sum(
        calls.get(k, 0) * max(settle-k, 0) + puts.get(k, 0) * max(k-settle, 0)
        for k in strikes
    ))


def _history_scores(chain, previous_rows, elapsed_seconds):
    if not previous_rows or elapsed_seconds is None or elapsed_seconds < _HISTORY_WINDOW_SECONDS:
        return 0.0, False, 0.0, False
    previous = {
        (row.get("type"), _number(row.get("strike"))): row
        for row in previous_rows if _number(row.get("strike")) is not None
    }
    metrics, flow, locked = {}, 0.0, 0.0
    oi_matched = oi_count = flow_matched = 0
    for row in chain.get("rows") or []:
        side, strike = row.get("type"), _number(row.get("strike"))
        oi, ltp = _number(row.get("oi")), _number(row.get("ltp"))
        old = previous.get((side, strike))
        old_oi = _number(old.get("oi")) if old else None
        if side not in {"CE", "PE"} or strike is None or oi is None:
            continue
        oi_count += 1
        if old_oi is None:
            continue
        oi_matched += 1
        item = metrics.setdefault(strike, {"window": 5, "strike": strike,
            "ceNow": 0.0, "ceDOI": 0.0, "peNow": 0.0, "peDOI": 0.0})
        prefix = "ce" if side == "CE" else "pe"
        doi = oi - old_oi
        item[prefix+"Now"], item[prefix+"DOI"] = oi, doi
        # OI velocity needs only current and prior OI. Keep its availability
        # independent from option LTP, which can be absent for illiquid legs.
        if ltp is not None:
            flow_matched += 1
            locked += oi * ltp
            flow += doi * ltp if side == "PE" else -doi * ltp
    # Commodity feeds often omit OI for some visible strikes. Requiring half
    # of every current row to have a matched baseline can hide valid 5-minute
    # velocity even when multiple CE/PE contracts have comparable snapshots.
    # Availability is based on actual matched OI pairs; LTP coverage remains
    # an independent gate for capital flow.
    velocity_available = oi_matched > 0
    flow_available = flow_matched > 0
    if not velocity_available:
        return 0.0, False, 0.0, False
    strikes = sorted(metrics)
    step = min((b-a for a, b in zip(strikes, strikes[1:]) if b > a), default=0)
    context = SimpleNamespace(verdicts={}, active_signals=[], oi_annotations={})
    velocity = score_oi_velocity(pd.DataFrame(metrics.values()),
        _number(chain.get("spot")) or _number(chain.get("atm_strike")) or 0,
        step, context, window=5,
        # MCX 5-minute OI deltas are much smaller than the equity default;
        # score only strikes with a measurable move so unchanged contracts do
        # not dilute the signal back to zero.
        mild_threshold=0.003, active_strikes_only=True)
    return (velocity, True,
            score_capital_flow(
                flow, locked, noise_ratio=0.0005, full_score_ratio=0.005
            ) if flow_available and locked > 0 else 0.0,
            flow_available and locked > 0)


def analyze_option_chain(chain: dict, *, previous_rows=None, elapsed_seconds=None,
                         price_closes=None, required_evidence=None, evidence_labels=None):
    """Analyze normalized CE/PE rows plus any available tick or polling context."""
    rows = chain.get("rows") or []
    calls = [r for r in rows if r.get("type") == "CE"]
    puts = [r for r in rows if r.get("type") == "PE"]

    def total(group, key):
        vals = [_number(r.get(key)) for r in group]
        vals = [v for v in vals if v is not None]
        return round(sum(vals), 2) if vals else None

    def wall(group):
        vals = [(_number(r.get("oi")), r.get("strike")) for r in group]
        vals = [(v, k) for v, k in vals if v is not None and v > 0]
        return max(vals, key=lambda x: x[0])[1] if vals else None

    call_oi, put_oi = total(calls, "oi"), total(puts, "oi")
    call_vol, put_vol = total(calls, "volume"), total(puts, "volume")
    raw_pcr = put_oi / call_oi if call_oi and put_oi is not None else None
    pcr = round(raw_pcr, 2) if raw_pcr is not None else None
    fut_change = _number(chain.get("future_change_pct"), allow_negative=True)
    pcr_score = score_pcr(raw_pcr) if raw_pcr is not None else 0.0
    fut_score = score_engine_bias("Bullish" if fut_change is not None and fut_change > 0
        else "Bearish" if fut_change is not None and fut_change < 0 else "Neutral")
    pain = _max_pain(rows)
    spot, dte = _number(chain.get("spot")), _expiry_dte(chain.get("expiry"))
    pain_score = 0.0
    if pain is not None and spot is not None and spot > 0:
        pain_score = score_max_pain(spot, pain, abs(spot-pain), dte, 0.0,
                                    SimpleNamespace(verdicts={}, active_signals=[]))
    vel, vel_ok, flow, flow_ok = _history_scores(chain, previous_rows, elapsed_seconds)
    scores = {"pcr": pcr_score, "engine_bias": 0.0, "futures": fut_score,
              "max_pain": pain_score, "oi_velocity": vel, "capital_flow": flow}
    available = {"pcr": raw_pcr is not None, "engine_bias": False,
                 "futures": fut_change is not None,
                 "max_pain": pain is not None and spot is not None and spot > 0,
                 "oi_velocity": vel_ok, "capital_flow": flow_ok}
    evidence = aggregate_evidence(weights=DECISION_EVIDENCE_WEIGHTS, scores=scores,
        availability=available, labels={**DECISION_EVIDENCE_LABELS, **(evidence_labels or {})})
    pos, neg = evidence["positiveContributors"], evidence["negativeContributors"]
    # Keep conflict policy centralized with weighted evidence aggregation.
    # A raw contributor-count check here would bypass its weight threshold.
    conflict = evidence["conflict"]
    bias, strength = derive_bias(evidence["composite"], conflict)
    required_missing = any(k in (required_evidence or {"pcr", "futures"})
                          for k in evidence["missingInputs"])
    confidence = compute_confidence(evidence["composite"], conflict, "", pos, neg, dte,
        pcr_score, vel, evidence_coverage=evidence["evidenceCoverage"]/100,
        critical_inputs_missing=required_missing)
    weighted = [{**c, "direction": "UNAVAILABLE" if not c["available"] else
        "BULLISH" if c["score"] > .15 else "BEARISH" if c["score"] < -.15 else "NEUTRAL"}
        for c in evidence["contributors"]]
    short = None
    if price_closes is not None:
        short = detect_short_horizon(str(chain.get("symbol") or "MARKET"),
            oi_score=vel if vel_ok else 0.0, fut_score=fut_score if fut_change is not None else 0.0,
            fut_ltp=spot or 0.0, price_closes=price_closes)
    price_move_5m = None
    if price_closes and len(price_closes) >= 2:
        latest_minute, latest_price = price_closes[-1]
        baseline = next((item for item in reversed(price_closes[:-1])
                         if 4 <= latest_minute - item[0] <= 7), None)
        if baseline and baseline[1] > 0:
            price_move_5m = round((latest_price / baseline[1] - 1) * 100, 4)
    momentum_conflict = bool(
        not required_missing and price_move_5m is not None and (
            (bias == "BULLISH" and price_move_5m <= -0.15)
            or (bias == "BEARISH" and price_move_5m >= 0.15)
        )
    )
    if momentum_conflict:
        conflict = True
        confidence = min(confidence, 40)
    verdict = "WAIT" if required_missing else "NEUTRAL" if conflict else bias
    if required_missing:
        reason = "Insufficient option OI or futures-change data to form a directional view."
    elif momentum_conflict:
        direction = "up" if price_move_5m > 0 else "down"
        reason = (f"Option positioning leans {bias.lower()}, but price moved {direction} "
                  f"{abs(price_move_5m):.2f}% over roughly five minutes; wait for alignment.")
    elif conflict:
        reason = "Available evidence points in different directions; wait for confirmation."
    elif verdict in {"BULLISH", "BEARISH"}:
        direction = 1 if verdict == "BULLISH" else -1
        sources = [c["label"] for c in evidence["contributors"] if c["available"] and
                   c["score"] is not None and c["score"] * direction > .15]
        reason = f"Shared evidence leans {verdict.lower()}" + (f" on {', '.join(sources)}." if sources else ".")
    else:
        reason = "Available evidence is neutral in the visible strike window."
    result = {"verdict": verdict, "reason": reason, "bias_strength": strength,
        "confidence": confidence, "evidence_coverage": evidence["evidenceCoverage"],
        "missing_inputs": evidence["missingInputs"], "contributors": evidence["contributors"],
        "evidence_weights": weighted, "put_call_oi_ratio": pcr, "call_oi": call_oi,
        "put_oi": put_oi, "call_volume": call_vol, "put_volume": put_vol,
        "call_oi_wall": wall(calls), "put_oi_wall": wall(puts),
        "futures_change_pct": fut_change, "visible_window_max_pain": pain,
        "price_move_5m_pct": price_move_5m, "momentum_conflict": momentum_conflict,
        "oi_velocity_window_seconds": _HISTORY_WINDOW_SECONDS if vel_ok else None,
        "capital_flow_window_seconds": _HISTORY_WINDOW_SECONDS if flow_ok else None,
        "scope": "Visible strike window; max pain is a visible-window estimate; OI is current positioning; velocity and capital flow compare five-minute snapshots.",
        "method": "Shared weighted evidence scores use available normalized chain, futures, tick, and polling data. Five-minute commodity OI velocity scores matched contracts with at least a 0.3% OI change; commodity capital flow has a 0.05% noise floor and reaches full score at 0.5%; confidence is not a probability."}
    if short is not None:
        result["short_horizon"] = short
    return result
