"""Bias direction + confidence scoring.

Split out of decision_engine.py's DecisionEngine class. Pure move +
de-methodize — neither function used instance state, only their own
arguments. No logic changes.
"""

from __future__ import annotations
from typing import Tuple

from decision.types import T


def derive_bias(score: float, conflict: bool) -> Tuple[str, str]:
    """
    Direction always comes from the weighted composite `score` — never
    from the raw pos/neg sub-signal headcount used to set `conflict`.
    Those two checks measure different things (weighted magnitude vs.
    unweighted count) and can legitimately disagree: e.g. PCR + engine
    bias (52% combined weight) can be strongly bullish while 4 minor,
    lightly-weighted signals (fut/maxPain/OI-vel/smart-money, 48%
    combined) each lean just past the 0.15 noise floor in the other
    direction. Discarding the composite's direction in that case would
    mislabel a genuinely bullish read as flatly "CONFLICTED".

    Instead: conflict downgrades conviction (forces WEAK strength, which
    downstream already routes to a WAIT action) while still reporting
    which way the composite leans. `conflict_flag` — set by the caller
    alongside this call — is what actually surfaces the disagreement to
    the UI (⚡ badge), so direction doesn't need to be sacrificed to
    communicate it.
    """
    a = abs(score)
    if a < 0.15:
        direction, strength = "NEUTRAL", "WEAK"
    else:
        strength = "MODERATE" if a < 0.40 else "STRONG"
        direction = "BULLISH" if score > 0 else "BEARISH"

    if conflict:
        strength = "WEAK"

    return direction, strength


def compute_confidence(
        composite: float, conflict: bool,
        vix_tag: str, pos_count: int, neg_count: int,
        dte: int, pcr_score: float, oi_score: float,
        sm_score: float = 0.0,
        evidence_coverage: float = 1.0,
        critical_inputs_missing: bool = False) -> int:
    """
    Base from composite magnitude, then modulate by:
    - Signal confluence (how many sub-scores agree)
    - VIX regime alignment
    - DTE decay
    - Volume-confirmed OI velocity bonus
    - Smart money confirmation bonus
    - Conflict penalty
    """
    base = abs(composite) * 60                    # max 60 from pure direction strength

    # Confluence bonus — each agreeing additional signal adds 5 pts (max +20)
    agree_count = pos_count if composite > 0 else neg_count
    confluence_bonus = min(20, max(0, (agree_count - 1) * 5))
    base += confluence_bonus

    # VIX alignment: low VIX + bearish (sell premium edge) or high VIX + bullish
    if vix_tag == "LOW"  and composite < 0:   base += 10
    if vix_tag == "LOW"  and composite > 0:   base +=  5
    if vix_tag in ("VERY_HIGH", "PANIC"):      base -=  8

    # OI velocity confirms (volume-boosted so worth slightly more than before)
    if (oi_score < 0 and composite < 0) or (oi_score > 0 and composite > 0):
        base += 8   # was 7; +1 because oi_score is now volume-confirmed

    # PCR extreme confirms
    if (pcr_score >= 0.65 and composite > 0) or (pcr_score <= -0.65 and composite < 0):
        base += 8

    # Smart money agrees with direction → extra conviction (+5 max)
    if (sm_score > 0.2 and composite > 0) or (sm_score < -0.2 and composite < 0):
        base += 5

    # DTE discount
    if   dte == 0: base *= 0.55
    elif dte == 1: base *= 0.75
    elif dte == 2: base *= 0.88

    # Conflict hard-cap
    if conflict:
        base = min(base, 40)

    # Confidence means agreement supported by available evidence, not a
    # probability of profit. Missing optional evidence scales the score;
    # missing a required directional input additionally caps it.
    coverage = max(0.0, min(1.0, float(evidence_coverage)))
    base *= coverage
    if critical_inputs_missing:
        base = min(base, 35)

    return min(95, max(0, int(base)))
