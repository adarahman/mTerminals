"""Shared thresholds and output dataclasses for the decision/ package.

Extracted from decision_engine.py so signal_builder.py, confidence.py, and
strategy_selection.py can import T / ActiveSignal / DecisionResult without
reaching into decision_engine.py itself. Pure move — no field, default, or
threshold changes.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List


# ── Thresholds ────────────────────────────────────────────────────────────────
# Tune per symbol: BANKNIFTY → MP_GRAVITY ~150, OI_VEL_STRONG ~0.20

class T:
    # PCR: PE_OI / CE_OI
    # HIGH PCR (>1.2) → heavy put writing → BULLISH
    # LOW  PCR (<0.8) → heavy call writing → BEARISH
    PCR_BULL_EXTREME  = 1.40   # very strong bullish
    PCR_BULL          = 1.20
    PCR_BEAR          = 0.80
    PCR_BEAR_EXTREME  = 0.60   # very strong bearish
    PCR_NEUTRAL_HI    = 1.10
    PCR_NEUTRAL_LO    = 0.90

    VIX_LOW    = 13.0          # sell-premium regime
    VIX_NORMAL = 18.0
    VIX_HIGH   = 22.0          # reduce short gamma
    VIX_PANIC  = 26.0          # long vol only

    MP_GRAVITY      = 80       # pts; strong pull below this
    MP_PIN          = 30       # pts; pin-zone
    OI_VEL_MILD     = 0.08
    OI_VEL_MODERATE = 0.15
    OI_VEL_STRONG   = 0.25

    IV_LOW     = 30            # iv_rank
    IV_MID     = 50
    IV_HIGH    = 65
    IV_EXTREME = 80

    # Below this confidence (or on WAIT/conflict), a strategy is still
    # computed and shown (so the person can see what the engine WOULD do),
    # but should not be presented as execute-ready.
    CONFIDENCE_EXECUTE_MIN = 40

    # IV crush: a fast VIX drop from its recent peak while positions are
    # still open (buildup, not unwinding) — the classic post-event trap
    # where Vega losses eat a correctly-directioned Delta gain.
    IV_CRUSH_WINDOW_SECONDS = 300   # look back this far for the recent peak
    IV_CRUSH_PCT            = 8.0   # % drop from peak that counts as a crush
    IV_CRUSH_MAX_AGE_SECONDS = 900  # prune history older than this


# ── Signal priority order (lower = shown first) ───────────────────────────────
_SEVERITY_ORDER = {"warn": 0, "ok": 1, "info": 2}


# ── Output types ──────────────────────────────────────────────────────────────

@dataclass
class ActiveSignal:
    text:     str
    severity: str = "info"     # "ok" | "info" | "warn"
    priority: int = 99         # lower surfaces first


@dataclass
class DecisionResult:
    # The decision is a point-in-time interpretation of one exported market
    # state.  These fields let consumers prove which state they are showing
    # and fail closed when that evidence is incomplete or old.
    decision_timestamp: str = ""
    state_version:       str = ""
    stale:               bool = False
    degraded:            bool = False
    evidence_coverage:   int = 0
    missing_inputs:      List[str] = field(default_factory=list)
    contributors:        List[dict] = field(default_factory=list)

    # ── Headline block ────────────────────────────────────────────────────────
    bias:           str = "NEUTRAL"   # BULLISH | BEARISH | NEUTRAL — direction always
                                       # from the weighted composite. See conflict_flag
                                       # for sub-signal disagreement (no longer folded
                                       # into bias itself — direction is preserved).
    bias_strength:  str = "WEAK"      # WEAK | MODERATE | STRONG — forced WEAK on conflict
    confidence:     int = 0           # 0–95
    conflict_flag:  bool = False       # True when sub-signals disagree

    # ── Action block ──────────────────────────────────────────────────────────
    action:             str          = ""
    action_type:        str          = "WAIT"
    suggested_strike:   Optional[int]= None

    # ── Strategy block ────────────────────────────────────────────────────────
    suggested_strategy: str  = ""
    auto_strategy:      dict = field(default_factory=dict)
    # Whether the auto_strategy above should be presented as execute-ready.
    # _suggest_strategy() always returns *a* strategy (even under WAIT, it
    # picks the range-appropriate one, e.g. Iron Condor when NEUTRAL) — that
    # part is correct. What was missing is a flag tying the Execute button's
    # state back to the same WAIT/conflict/confidence read shown in the
    # headline block, so the two panels can't visually disagree.
    execute_recommended: bool = True
    strategy_caution:    str  = ""   # human-readable reason(s) when False

    # ── Supporting info ───────────────────────────────────────────────────────
    active_signals:  List[ActiveSignal] = field(default_factory=list)
    verdicts:        dict = field(default_factory=dict)
    oi_annotations:  dict = field(default_factory=dict)
    trade_grade:     str = ""
    risk_warning:    str = ""
    important_levels: dict = field(default_factory=dict)

    # ── Score debug (strip in prod if desired) ────────────────────────────────
    _debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        sigs = sorted(self.active_signals,
                      key=lambda s: (_SEVERITY_ORDER.get(s.severity, 9), s.priority))
        return {
            "decisionTimestamp": self.decision_timestamp,
            "stateVersion":      self.state_version,
            "stale":             self.stale,
            "degraded":          self.degraded,
            "evidenceCoverage":  self.evidence_coverage,
            "missingInputs":     self.missing_inputs,
            "contributors":      self.contributors,
            "bias":              self.bias,
            "biasStrength":      self.bias_strength,
            "confidence":        self.confidence,
            "conflictFlag":      self.conflict_flag,
            "action":            self.action,
            "actionType":        self.action_type,
            "suggestedStrike":   self.suggested_strike,
            "suggestedStrategy": self.suggested_strategy,
            "executeRecommended": self.execute_recommended,
            "strategyCaution":    self.strategy_caution,
            "activeSignals":     [{"text": s.text, "severity": s.severity}
                                  for s in sigs],
            "verdicts":          self.verdicts,
            "oiAnnotations":     self.oi_annotations,
            "tradeGrade":        self.trade_grade,
            "riskWarning":       self.risk_warning,
            "importantLevels":   self.important_levels,
            "autoStrategy":      self.auto_strategy,
            "_debug":            self._debug,
        }
